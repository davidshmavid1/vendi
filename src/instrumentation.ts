export async function register() {
  // Registers all domain event handlers exactly once when the server starts.
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("@/domain/events/handlers");
  }
}
