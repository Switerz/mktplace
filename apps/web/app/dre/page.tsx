import { Suspense } from "react";

import DreClient from "./DreClient";

/**
 * Tela "DRE" — DRE-UX-1. `useSearchParams` exige Suspense no App Router: sem
 * ele o build falha ao pre-renderizar a rota.
 */
export default function DrePage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <DreClient />
    </Suspense>
  );
}
