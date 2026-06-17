import { Link } from 'react-router-dom'

export function NavBar() {
  return (
    <nav>
      <span className="nav-title">NV Battle Reports</span>
      <Link to="/">Timeline</Link>
      <span className="nav-coming-soon">Logs — coming soon</span>
    </nav>
  )
}
