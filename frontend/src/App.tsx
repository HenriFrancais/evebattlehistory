import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { DevBar } from './components/DevBar'
import { BrDetailPage } from './views/BrDetailPage'
import { BrListPage } from './views/BrListPage'
import { CharacterTimelinePage } from './views/CharacterTimelinePage'
import { CreatePage } from './views/CreatePage'
import { FightDetailPage } from './views/FightDetailPage'
import { LogsPage } from './views/LogsPage'

export function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <DevBar />
      <Routes>
        <Route path="/" element={<BrListPage />} />
        <Route path="/brs/new" element={<CreatePage />} />
        <Route path="/brs/:id" element={<BrDetailPage />} />
        <Route path="/brs/:id/fights/:fid" element={<FightDetailPage />} />
        <Route path="/brs/:id/characters/:charId" element={<CharacterTimelinePage />} />
        <Route path="/logs" element={<LogsPage />} />
      </Routes>
    </BrowserRouter>
  )
}
