(function () {
    // Floating tooltip element
    var tip = document.createElement('div');
    tip.id = 'oi-seg-tip';
    tip.style.cssText = [
        'position:fixed',
        'padding:4px 8px',
        'background:rgba(30,30,30,0.85)',
        'color:#fff',
        'border-radius:4px',
        'font-size:11px',
        'pointer-events:none',
        'display:none',
        'z-index:9999',
        'white-space:nowrap',
        'box-shadow:0 2px 6px rgba(0,0,0,0.3)',
    ].join(';');
    document.body.appendChild(tip);

    // Track cursor position for tooltip placement
    document.addEventListener('mousemove', function (e) {
        tip.style.left = (e.clientX + 14) + 'px';
        tip.style.top  = (e.clientY - 32) + 'px';
    });

    function highlight(card, signal) {
        card.querySelectorAll('[data-signal]').forEach(function (el) {
            el.style.opacity = (el.dataset.signal === signal) ? '1' : '0.2';
        });
    }

    function clearHighlight(card) {
        card.querySelectorAll('[data-signal]').forEach(function (el) {
            el.style.opacity = '1';
        });
    }

    // mouseover — fires for every element including children (bubbles)
    document.addEventListener('mouseover', function (e) {
        var seg = e.target.closest('[data-signal]');
        if (seg) {
            var card = seg.closest('[data-card]');
            if (!card) return;
            highlight(card, seg.dataset.signal);
            tip.textContent = seg.dataset.signal + ': ' + seg.dataset.pct + '% (' + seg.dataset.count + 'd)';
            tip.style.display = 'block';
        } else {
            // Hovering a non-segment area inside a card — clear highlight
            var card = e.target.closest('[data-card]');
            if (card) {
                clearHighlight(card);
                tip.style.display = 'none';
            }
        }
    });

    // mouseout — clear when the cursor leaves the card entirely
    document.addEventListener('mouseout', function (e) {
        var card = e.target.closest('[data-card]');
        if (!card) return;
        var to = e.relatedTarget;
        if (to && card.contains(to)) return;   // still inside the card
        clearHighlight(card);
        tip.style.display = 'none';
    });
}());
