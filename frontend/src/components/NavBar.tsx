import { Link } from 'react-router-dom'

export function NavBar() {
  return (
    <nav>
      <span className="nav-title">NV Battle Reports</span>
      <Link to="/">Timeline</Link>
      <Link to="/logs">Logs</Link>
    </nav>
  )
}
