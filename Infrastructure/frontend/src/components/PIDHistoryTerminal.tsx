import { useState, useEffect, useRef } from 'react';
import { apiClient } from '../services/api';
import type { PIDHistoryEntry } from '../types/pid';
import { logger } from '../utils/logger';

interface PIDHistoryTerminalProps {
  deviceType: string;
  maxLines?: number;
}

export default function PIDHistoryTerminal({ deviceType, maxLines = 50 }: PIDHistoryTerminalProps) {
  const [history, setHistory] = useState<PIDHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Format timestamp like [2026-01-16 11:30:22]
  const formatTimestamp = (isoString: string) => {
    try {
      const date = new Date(isoString);
      return `[${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}]`;
    } catch (e) {
      return `[${isoString}]`;
    }
  };

  const fetchHistory = async () => {
    try {
      setLoading(true);
      const data = await apiClient.getPIDParameterHistory(deviceType, maxLines);
      setHistory(data);
    } catch (error) {
      logger.error('Error fetching PID history:', error);
    } finally {
      setLoading(false);
    }
  };

  // Poll for updates every 30 seconds
  useEffect(() => {
    fetchHistory();

    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    pollIntervalRef.current = setInterval(fetchHistory, 30000);

    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, [deviceType, maxLines]);

  // Auto-scroll to top (newest) or bottom depending on order
  // Assuming API returns newest first (standard for logs/history lists usually)
  // But terminal usually appends to bottom. Let's render in reverse chronological order or chronological?
  // Terminal usually shows history scrolling UP. So newest at BOTTOM.
  // If API returns newest first (descending), we should reverse it for display so newest is at the bottom.
  useEffect(() => {
    if (scrollRef.current && !isCollapsed) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [history, isCollapsed]);

  // Display newest at bottom
  const displayHistory = [...history].sort((a, b) => 
    new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );

  return (
    <div className="w-full mt-6 font-mono text-sm border border-gray-700 rounded-md overflow-hidden bg-gray-900 shadow-lg">
      {/* Terminal Header */}
      <div 
        className="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700 cursor-pointer hover:bg-gray-750 transition-colors"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <div className="flex items-center gap-2 text-gray-200">
          <span>📟</span>
          <span className="font-bold">Auto-PID History</span>
          <span className="text-xs text-gray-400 ml-2">({deviceType})</span>
        </div>
        <div className="flex items-center gap-4">
          <button 
            onClick={(e) => {
              e.stopPropagation();
              fetchHistory();
            }}
            className="text-xs text-blue-400 hover:text-blue-300 underline"
          >
            Refresh
          </button>
          <span className="text-gray-400 transform transition-transform duration-200" style={{ transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }}>
            ▼
          </span>
        </div>
      </div>

      {/* Terminal Content */}
      {!isCollapsed && (
        <div 
          ref={scrollRef}
          className="p-4 overflow-y-auto max-h-[400px] scrollbar-thin scrollbar-thumb-gray-600 scrollbar-track-transparent"
        >
          {loading && history.length === 0 ? (
            <div className="text-gray-500 italic text-center py-4">$ Loading history...</div>
          ) : history.length === 0 ? (
            <div className="text-gray-500 italic text-center py-4">$ No history recorded yet.</div>
          ) : (
            <div className="space-y-4">
              {displayHistory.map((entry, index) => (
                <div key={`${entry.timestamp}-${index}`} className="flex flex-col gap-1">
                  {/* Timestamp Line */}
                  <div className="flex items-center gap-2">
                    <span className="text-green-400 font-bold opacity-80">
                      {formatTimestamp(entry.timestamp)}
                    </span>
                    <span className="text-blue-300 opacity-90">{entry.device_type}</span>
                  </div>

                  {/* Values Change Line */}
                  <div className="pl-4 text-gray-200 border-l-2 border-gray-700 ml-1">
                    <div className="flex flex-wrap gap-4">
                      {['kp', 'ki', 'kd'].map(param => {
                        const p = param as keyof typeof entry.old_values;
                        const oldVal = entry.old_values?.[p];
                        const newVal = entry.new_values?.[p];
                        
                        // Skip if no change or values missing
                        if (oldVal === undefined || newVal === undefined) return null;
                        
                        const changed = Math.abs(oldVal - newVal) > 0.0001;
                        
                        return (
                          <span key={param} className={changed ? 'text-white font-semibold' : 'text-gray-500'}>
                            {param.toUpperCase()}: {oldVal.toFixed(3)} → {newVal.toFixed(3)}
                          </span>
                        );
                      })}
                    </div>
                    
                    {/* Reason / Info Line */}
                    <div className="mt-1 flex items-start gap-2 text-gray-400">
                      <span className="text-yellow-500">ℹ</span>
                      <span>{entry.reason || 'No reason recorded'}</span>
                    </div>

                    {/* Tuning Metrics if available */}
                    {entry.tuning_metrics && (
                       <div className="mt-1 ml-6 text-xs text-gray-500 font-mono">
                         Ku={entry.tuning_metrics.ku?.toFixed(2) ?? '?'}, 
                         Tu={entry.tuning_metrics.tu?.toFixed(1) ?? '?'}s
                       </div>
                    )}
                  </div>
                </div>
              ))}
              <div className="pt-2 text-gray-500 animate-pulse">
                $ Waiting for updates...
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
