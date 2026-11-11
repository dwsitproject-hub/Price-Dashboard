"""Idempotent reference-data bootstrap: roles, modules, products, instruments, series, first admin.

Run on every deploy (after migrations). Admin edits to role↔module assignments are preserved:
defaults are only inserted when a role is created.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from app.catalog.instruments import CASH, FX, PRODUCTS, all_rics, oi_extended_rics, parse_futures_ric
from app.catalog.registry import MODULES, ROLE_DEFAULT_MODULES, SERIES, ric_display
from app.core.config import get_settings
from app.core.security import hash_password, password_policy_errors
from app.models import Instrument, Module, Product, Role, RoleModule, Series, User

logger = logging.getLogger("app.bootstrap")

ROLES = [
    ("admin", "Administrator", "Full access, including the admin console.", ["admin", "data.export",
                                                                            "pipeline.run"]),
    ("analyst", "Analyst", "All analytics modules, CSV export, can refresh data.", ["data.export", "pipeline.run"]),
    ("viewer", "Viewer", "Read-only access to a curated set of modules.", []),
]


def sync_roles_modules(db: DbSession) -> None:
    for m in MODULES:
        row = db.get(Module, m.code)
        if row is None:
            db.add(Module(code=m.code, name=m.name, description=m.description, sort_order=m.sort_order,
                          icon=m.icon, release="R1"))
        else:
            row.name, row.description, row.sort_order, row.icon = m.name, m.description, m.sort_order, m.icon
    db.flush()
    for code, name, desc, perms in ROLES:
        role = db.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name=name, description=desc, permissions=perms, is_system=True)
            db.add(role)
            db.flush()
            for mc in ROLE_DEFAULT_MODULES[code]:
                db.add(RoleModule(role_id=role.id, module_code=mc))
        else:
            role.name, role.description, role.permissions, role.is_system = name, desc, perms, True
    db.flush()


def sync_instruments(db: DbSession) -> None:
    for p in PRODUCTS.values():
        db.merge(Product(code=p.code, display_name=p.display_name, oi_display_name=p.oi_display_name,
                         exchange=p.exchange, native_unit=p.native_unit, conversion_type=p.conversion_type,
                         conversion_factor=p.conversion_factor, conversion_fx_ric=p.conversion_fx_ric,
                         oi_contracts=p.oi_contracts, oi_display_order=p.oi_display_order, sort_order=p.sort_order))
    db.flush()
    extended = set(oi_extended_rics())
    for ric in all_rics():
        fut = parse_futures_ric(ric)
        if fut:
            p = PRODUCTS[fut[0]]
            inst = Instrument(ric=ric, product_code=p.code, category="futures", contract_no=fut[1],
                              display_name=ric_display(ric), native_unit=p.native_unit,
                              conversion_type=p.conversion_type, conversion_factor=p.conversion_factor,
                              conversion_fx_ric=p.conversion_fx_ric, price_basis="last",
                              universe="oi_extended" if ric in extended else "core")
        elif ric in FX:
            f = FX[ric]
            inst = Instrument(ric=ric, category=f.category, display_name=f.display_name, native_unit=f.native_unit,
                              conversion_type="none", price_basis="mid", universe="core")
        else:
            c = CASH[ric]
            inst = Instrument(ric=ric, category=c.category, display_name=c.display_name, native_unit=c.native_unit,
                              conversion_type=c.conversion_type, conversion_fx_ric=c.conversion_fx_ric,
                              price_basis="last", universe="core")
        db.merge(inst)
    db.flush()


def sync_series(db: DbSession) -> None:
    keys = set()
    for s in SERIES:
        keys.add(s.key)
        db.merge(Series(key=s.key, module_code=s.module, group_name=s.group, title=s.title, kind=s.kind,
                        family=s.family, tenor=s.tenor, unit=s.unit, local_unit=s.local_unit,
                        definition=s.definition, sort_order=s.sort_order, is_active=True))
    db.flush()
    db.execute(text("UPDATE market.series SET is_active = false WHERE NOT (key = ANY(:k))"), {"k": list(keys)})


def ensure_admin(db: DbSession) -> str | None:
    s = get_settings()
    has_admin = db.scalar(select(func.count()).select_from(User).join(Role)
                          .where(Role.permissions.any("admin"), User.status == "active"))
    if has_admin:
        return None
    if not s.bootstrap_admin_email or not s.bootstrap_admin_password:
        logger.warning("No active administrator and BOOTSTRAP_ADMIN_EMAIL/PASSWORD not set.")
        return None
    errs = password_policy_errors(s.bootstrap_admin_password, s.bootstrap_admin_email)
    if errs:
        raise SystemExit("BOOTSTRAP_ADMIN_PASSWORD does not meet the password policy: " + " ".join(errs))
    role = db.scalar(select(Role).where(Role.code == "admin"))
    email = s.bootstrap_admin_email.strip().lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing:
        existing.role_id, existing.status = role.id, "active"
        return email
    db.add(User(email=email, display_name=s.bootstrap_admin_name, role_id=role.id, auth_provider="both",
                password_hash=hash_password(s.bootstrap_admin_password)))
    return email


def run(db: DbSession) -> dict:
    sync_roles_modules(db)
    sync_instruments(db)
    sync_series(db)
    admin = ensure_admin(db)
    db.commit()
    counts = db.execute(text("SELECT (SELECT count(*) FROM iam.modules), (SELECT count(*) FROM market.instruments), "
                             "(SELECT count(*) FROM market.series WHERE is_active)")).first()
    return {"modules": counts[0], "instruments": counts[1], "series": counts[2], "admin_created": admin}
