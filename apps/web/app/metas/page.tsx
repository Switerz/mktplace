import { Suspense } from "react";

import MetasClient from "./MetasClient";

/**
 * Tela "Metas" — METAS-1. `useSearchParams` exige Suspense no App Router: sem
 * ele o build falha ao pre-renderizar a rota.
 */
export default function MetasPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <MetasClient />
    </Suspense>
  );
}
