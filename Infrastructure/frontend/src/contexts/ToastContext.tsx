import { createContext, useContext, useState, useCallback, ReactNode } from 'react'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
 id: string
 message: string
 type: ToastType
 duration?: number
}

interface ToastContextType {
 toasts: Toast[]
 showToast: (message: string, type?: ToastType, duration?: number) => void
 removeToast: (id: string) => void
}

const ToastContext = createContext<ToastContextType | undefined>(undefined)

export function ToastProvider({ children }: { children: ReactNode }) {
 const [toasts, setToasts] = useState<Toast[]>([])

 const showToast = useCallback((message: string, type: ToastType = 'info', duration: number = 5000) => {
 const id = Math.random().toString(36).substring(7)
 const toast: Toast = { id, message, type, duration }
 
 setToasts(prev => [...prev, toast])
 
 if (duration > 0) {
 setTimeout(() => {
 removeToast(id)
 }, duration)
 }
 }, [])

 const removeToast = useCallback((id: string) => {
 setToasts(prev => prev.filter(toast => toast.id !== id))
 }, [])

 return (
 <ToastContext.Provider value={{ toasts, showToast, removeToast }}>
 {children}
 <ToastContainer toasts={toasts} removeToast={removeToast} />
 </ToastContext.Provider>
 )
}

export function useToast() {
 const context = useContext(ToastContext)
 if (!context) {
 throw new Error('useToast must be used within ToastProvider')
 }
 return context
}

function ToastContainer({ toasts, removeToast }: { toasts: Toast[], removeToast: (id: string) => void }) {
 return (
 <div className="fixed top-4 right-4 z-50 space-y-2">
 {toasts.map(toast => (
 <div
 key={toast.id}
 className={`
 px-4 py-3 rounded-lg shadow-lg max-w-md
 ${toast.type === 'success' ? 'bg-status-success-vivid text-text-default' : ''}
 ${toast.type === 'error' ? 'bg-status-danger-vivid text-text-default' : ''}
 ${toast.type === 'warning' ? 'bg-status-warning-vivid text-text-default' : ''}
 ${toast.type === 'info' ? 'bg-btn-primary-light text-text-default' : ''}
 animate-in slide-in-from-top-5 fade-in
 `}
 onClick={() => removeToast(toast.id)}
 >
 <div className="flex items-center justify-between">
 <p className="text-sm font-medium">{toast.message}</p>
 <button
 onClick={() => removeToast(toast.id)}
 className="ml-4 text-text-default hover:text-text-input"
 >
 ×
 </button>
 </div>
 </div>
 ))}
 </div>
 )
}
