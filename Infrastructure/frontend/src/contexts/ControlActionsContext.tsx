import { createContext, useContext, useState, ReactNode } from 'react';

interface ControlActions {
  roomName?: string;
  showActions?: boolean;
  saving?: boolean;
  saveSuccess?: string | null;
  saveError?: string | null;
  currentMode?: { mode_name?: string; submode_name?: string | null } | null;
  onSave?: () => void;
  onModeChange?: (mode: string, submode?: string) => void;
}

const ControlActionsContext = createContext<{
  actions: ControlActions;
  setActions: (actions: ControlActions) => void;
}>({
  actions: {},
  setActions: () => {},
});

export function ControlActionsProvider({ children }: { children: ReactNode }) {
  const [actions, setActions] = useState<ControlActions>({});
  return (
    <ControlActionsContext.Provider value={{ actions, setActions }}>
      {children}
    </ControlActionsContext.Provider>
  );
}

export function useControlActions() {
  return useContext(ControlActionsContext);
}
