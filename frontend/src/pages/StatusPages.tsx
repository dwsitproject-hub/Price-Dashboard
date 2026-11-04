import { Link } from 'react-router-dom';
import { Icon } from '../components/Icon';

export function ForbiddenPage() {
  return (
    <div className="card card-pad" style={{ maxWidth: 560 }}>
      <div className="stack" style={{ gap: 10 }}>
        <span className="badge badge-neg" style={{ alignSelf: 'flex-start' }}><Icon name="lock" size={13} /> 403</span>
        <h2>You don’t have access to this module</h2>
        <p className="muted" style={{ margin: 0 }}>Ask an AASTAR administrator to grant it to your role or account.</p>
        <Link to="/" className="btn" style={{ alignSelf: 'flex-start' }}>Back to overview</Link>
      </div>
    </div>
  );
}

export function NotFoundPage() {
  return (
    <div className="card card-pad" style={{ maxWidth: 560 }}>
      <div className="stack" style={{ gap: 10 }}>
        <span className="badge" style={{ alignSelf: 'flex-start' }}>404</span>
        <h2>Page not found</h2>
        <p className="muted" style={{ margin: 0 }}>The page you’re looking for doesn’t exist or has moved.</p>
        <Link to="/" className="btn" style={{ alignSelf: 'flex-start' }}>Back to overview</Link>
      </div>
    </div>
  );
}
