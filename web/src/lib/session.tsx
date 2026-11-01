import { createContext, useContext, type ReactNode } from "react";
import { useApi } from "./hooks";

export interface Me {
  user_id: string;
  display_name: string;
  role: string;
  auth_method: string;
  workspace: { id: string; slug: string; name: string } | null;
  permissions: string[];
}

interface Session {
  me: Me | undefined;
  can: (permission: string) => boolean;
  authError: boolean;
  reload: () => void;
}

const SessionContext = createContext<Session>({ me: undefined, can: () => false, authError: false, reload: () => {} });

export function SessionProvider({ children }: { children: ReactNode }) {
  const { data, error, reload } = useApi<Me>("/api/v1/me");
  const can = (permission: string) =>
    Boolean(data && (data.permissions.includes("*") || data.permissions.includes(permission)));
  return (
    <SessionContext.Provider value={{ me: data, can, authError: error?.status === 401, reload }}>
      {children}
    </SessionContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export const useSession = () => useContext(SessionContext);
