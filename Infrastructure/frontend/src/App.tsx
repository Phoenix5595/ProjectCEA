import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext'
import { ToastProvider } from './contexts/ToastContext'
import ThemeSwitcher from './components/ThemeSwitcher'
import Dashboard from './pages/Dashboard'
import ZoneConfig from './pages/ZoneConfig'
import DeviceConfig from './pages/DeviceConfig'

function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/zone/:location/:cluster" element={<ZoneConfig />} />
            <Route path="/device-config" element={<DeviceConfig />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
      <ThemeSwitcher />
    </ThemeProvider>
  )
}

export default App

