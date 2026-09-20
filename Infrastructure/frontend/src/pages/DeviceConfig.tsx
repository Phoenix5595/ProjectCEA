import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
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
 <ArrowLeft className="size-5" />
 Back to Dashboard
 </Link>
 </div>
 <DeviceManager />
 </div>
 </div>
 )
}
