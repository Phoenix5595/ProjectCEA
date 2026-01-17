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
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Format timestamp like [11:30:22]
  const formatTimestamp = (isoString: string) => {
    try {
      const date = new Date(isoString);
      return `[${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}]`;
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

  // Display newest at top
  const displayHistory = [...history].sort((a, b) => 
    new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  return (
    <div className="w-full font-mono text-[10px] bg-black rounded p-1 max-h-[120px] overflow-y-auto scrollbar-thin scrollbar-thumb-gray-800 scrollbar-track-transparent">
        {loading && history.length === 0 ? (
          <div className="text-gray-600 italic px-1">$ Loading...</div>
        ) : history.length === 0 ? (
          <div className="text-gray-600 italic px-1">$ No history.</div>
        ) : (
          <div className="space-y-0.5">
            {displayHistory.map((entry, index) => (
              <div key={`${entry.timestamp}-${index}`} className="flex gap-1 border-b border-gray-900 pb-0.5">
                <span className="text-gray-500 whitespace-nowrap">
                  {formatTimestamp(entry.timestamp)}
                </span>

                <div className="text-gray-300">
                  <span className="text-yellow-700 mr-1">{entry.reason || 'manual'}:</span>
                  {['kp', 'ki', 'kd'].map(param => {
                    const p = param as keyof typeof entry.old_values;
                    const oldVal = entry.old_values?.[p];
                    const newVal = entry.new_values?.[p];
                    
                    if (oldVal === undefined || newVal === undefined) return null;
                    
                    const changed = Math.abs(oldVal - newVal) > 0.0001;
                    if (!changed) return null;
                    
                    return (
                      <span key={param} className="mr-1 text-cyan-500">
                        {param.toUpperCase()}:{newVal.toFixed(2)}
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}

