import type { Metadata } from "next";

export const metadata: Metadata = { title: "Operações" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
