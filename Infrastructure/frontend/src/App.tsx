import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { ThemeProvider } from './contexts/ThemeContext'
import { ControlActionsProvider } from './contexts/ControlActionsContext'
import ThemeSwitcher from './components/ThemeSwitcher'
import Layout from './components/Layout'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const DeviceConfig = lazy(() => import('./pages/DeviceConfig'))
const LaboratoryClimate = lazy(() => import('./pages/LaboratoryClimate'))
const LaboratoryWater = lazy(() => import('./pages/LaboratoryWater'))
const LaboratoryInfrastructure = lazy(() => import('./pages/LaboratoryInfrastructure'))
const VegetationMonitoring = lazy(() => import('./pages/VegetationMonitoring'))
const VegetationControl = lazy(() => import('./pages/VegetationControl'))
const FlowerMonitoring = lazy(() => import('./pages/FlowerMonitoring'))
const FlowerControl = lazy(() => import('./pages/FlowerControl'))
const FlowerSoil = lazy(() => import('./pages/FlowerSoil'))
const LaboratoryOverview = lazy(() => import('./pages/LaboratoryOverview'))
const VegetationOverview = lazy(() => import('./pages/VegetationOverview'))
const FlowerOverview = lazy(() => import('./pages/FlowerOverview'))

function App() {
  return (
    <ThemeProvider>
      <ControlActionsProvider>
        <Toaster position="top-right" richColors closeButton />
        <BrowserRouter>
          <Suspense fallback={<div className="flex items-center justify-center h-screen text-muted">Loading...</div>}>
            <Routes>
              <Route path="/zone/Veg Room/main" element={<Navigate to="/vegetation/control" replace />} />
              <Route path="/zone/Flower Room/main" element={<Navigate to="/flower/control" replace />} />
              <Route path="/device-config" element={<Navigate to="/devices" replace />} />

              <Route element={<Layout />}>
                <Route path="/" element={<Dashboard />} />

                <Route path="/laboratory" element={<LaboratoryOverview />} />
                <Route path="/laboratory/climate" element={<LaboratoryClimate />} />
                <Route path="/laboratory/water" element={<LaboratoryWater />} />
                <Route path="/laboratory/infrastructure" element={<LaboratoryInfrastructure />} />

                <Route path="/vegetation" element={<VegetationOverview />} />
                <Route path="/vegetation/monitoring" element={<VegetationMonitoring />} />
                <Route path="/vegetation/control" element={<VegetationControl />} />

                <Route path="/flower" element={<FlowerOverview />} />
                <Route path="/flower/monitoring" element={<FlowerMonitoring />} />
                <Route path="/flower/control" element={<FlowerControl />} />
                <Route path="/flower/soil" element={<FlowerSoil />} />

                <Route path="/devices" element={<DeviceConfig />} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
        <ThemeSwitcher />
      </ControlActionsProvider>
    </ThemeProvider>
  )
}

export default App
