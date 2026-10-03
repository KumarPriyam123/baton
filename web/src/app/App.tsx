import { HealthStatus } from "./HealthStatus";

export function App() {
  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="mb-2 text-2xl font-semibold text-stone-900">Baton</h1>
      <HealthStatus />
    </main>
  );
}
