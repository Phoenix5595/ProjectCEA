import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { ThemeProvider } from './contexts/ThemeContext'
import { ControlActionsProvider } from './contexts/ControlActionsContext'
import ErrorBoundary from './components/ErrorBoundary'
import ThemeSwitcher from './components/ThemeSwitcher'
import Layout from './components/Layout'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const DeviceConfig = lazy(() => import('./pages/DeviceConfig'))
const VegetationMonitoring = lazy(() => import('./pages/VegetationMonitoring'))
const VegetationControl = lazy(() => import('./pages/VegetationControl'))
const VegetationAutomation = lazy(() => import('./pages/VegetationAutomation'))
const FlowerMonitoring = lazy(() => import('./pages/FlowerMonitoring'))
const FlowerControl = lazy(() => import('./pages/FlowerControl'))
const FlowerAutomation = lazy(() => import('./pages/FlowerAutomation'))
const LaboratoryOverview = lazy(() => import('./pages/LaboratoryOverview'))
const VegetationOverview = lazy(() => import('./pages/VegetationOverview'))
const FlowerOverview = lazy(() => import('./pages/FlowerOverview'))
const CalendarSettings = lazy(() => import('./pages/CalendarSettings'))

function App() {
  return (
    <ThemeProvider>
      <ControlActionsProvider>
        <Toaster position="top-right" richColors closeButton />
        <BrowserRouter>
          <ErrorBoundary>
          <Suspense fallback={<div className="flex items-center justify-center h-screen text-muted">Loading...</div>}>
            <Routes>
              <Route path="/zone/Veg Room/main" element={<Navigate to="/vegetation/control" replace />} />
              <Route path="/zone/Flower Room/main" element={<Navigate to="/flower/control" replace />} />
              <Route path="/device-config" element={<Navigate to="/devices" replace />} />
              <Route path="/laboratory/climate" element={<Navigate to="/laboratory" replace />} />
              <Route path="/laboratory/water" element={<Navigate to="/laboratory" replace />} />
              <Route path="/laboratory/infrastructure" element={<Navigate to="/laboratory" replace />} />
              <Route path="/flower/soil" element={<Navigate to="/flower" replace />} />

              <Route element={<Layout />}>
                <Route path="/" element={<Dashboard />} />

                <Route path="/laboratory" element={<LaboratoryOverview />} />

                <Route path="/vegetation" element={<VegetationOverview />} />
                <Route path="/vegetation/monitoring" element={<VegetationMonitoring />} />
                <Route path="/vegetation/control" element={<VegetationControl />} />
                <Route path="/vegetation/automation" element={<VegetationAutomation />} />

                <Route path="/flower" element={<FlowerOverview />} />
                <Route path="/flower/monitoring" element={<FlowerMonitoring />} />
                <Route path="/flower/control" element={<FlowerControl />} />
                <Route path="/flower/automation" element={<FlowerAutomation />} />

                <Route path="/devices" element={<DeviceConfig />} />
                <Route path="/settings/calendar" element={<CalendarSettings />} />
              </Route>
            </Routes>
          </Suspense>
          </ErrorBoundary>
        </BrowserRouter>
        <ThemeSwitcher />
      </ControlActionsProvider>
    </ThemeProvider>
  )
}

export default App
