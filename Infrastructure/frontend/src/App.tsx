import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext'
import Dashboard from './pages/Dashboard'
import ZoneConfig from './pages/ZoneConfig'

function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/zone/:location/:cluster" element={<ZoneConfig />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  )
}

export default App

