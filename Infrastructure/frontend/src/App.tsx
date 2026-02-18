import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Toaster } from 'sonner'
import { ThemeProvider } from './contexts/ThemeContext'
import ThemeSwitcher from './components/ThemeSwitcher'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const ZoneConfig = lazy(() => import('./pages/ZoneConfig'))
const DeviceConfig = lazy(() => import('./pages/DeviceConfig'))

function App() {
  return (
    <ThemeProvider>
      <Toaster position="top-right" richColors closeButton />
      <BrowserRouter>
        <Suspense fallback={<div className="flex items-center justify-center h-screen text-muted">Loading...</div>}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/zone/:location/:cluster" element={<ZoneConfig />} />
            <Route path="/device-config" element={<DeviceConfig />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
      <ThemeSwitcher />
    </ThemeProvider>
  )
}

export default App

