import { Link } from 'react-router-dom'
import DeviceManager from '../components/DeviceManager'

export default function DeviceConfig() {
 return (
 <div className="min-h-screen bg-surface-base p-4">
 <div className="mx-auto max-w-[1800px]">
 <div className="mb-4 flex items-center gap-4">
 <Link
 to="/"
 className="text-text-muted hover:text-text-secondary transition-colors flex items-center gap-1"
 >
 <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
 </svg>
 Back to Dashboard
 </Link>
 </div>
 <DeviceManager />
 </div>
 </div>
 )
}
