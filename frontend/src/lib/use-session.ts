import { useSyncExternalStore } from "react";

import { sessionStore } from "@/api/client";
import type { SessionState } from "@/lib/session-store";

const SERVER_STATE: SessionState = { status: "unknown" };

/** Состояние сессии в компоненте (аналог `useStore` из Zustand). */
export function useSession(): SessionState {
  return useSyncExternalStore(
    sessionStore.subscribe,
    sessionStore.getState,
    () => SERVER_STATE,
  );
}
