import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import ZoneConfig from './pages/ZoneConfig'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/zone/:location/:cluster" element={<ZoneConfig />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App

