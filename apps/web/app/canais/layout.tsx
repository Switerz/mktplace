import type { Metadata } from "next";

export const metadata: Metadata = { title: "Canais" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
