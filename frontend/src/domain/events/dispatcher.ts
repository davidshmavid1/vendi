import type { DomainEvent, DomainEventType } from "./types";

type Handler<T extends DomainEventType> = (
  event: Extract<DomainEvent, { type: T }>,
) => Promise<void> | void;

/**
 * In-process pub/sub. `emit` is called after a state transition's DB
 * transaction has committed; handlers run concurrently and a failing
 * handler is logged, never thrown back into the request that triggered it.
 *
 * Phase 2: replace the body of `emit` with `queue.add(event.type, event)`
 * and have a worker call the same registered handlers — nothing about the
 * handler functions or the call sites that invoke `emit` needs to change.
 */
class EventDispatcher {
  private handlers = new Map<DomainEventType, Handler<DomainEventType>[]>();

  on<T extends DomainEventType>(type: T, handler: Handler<T>) {
    const list = this.handlers.get(type) ?? [];
    list.push(handler as unknown as Handler<DomainEventType>);
    this.handlers.set(type, list);
  }

  async emit(event: DomainEvent): Promise<void> {
    const list = this.handlers.get(event.type) ?? [];
    const results = await Promise.allSettled(list.map((handler) => handler(event)));
    for (const result of results) {
      if (result.status === "rejected") {
        console.error(`[events] handler for ${event.type} failed:`, result.reason);
      }
    }
  }
}

declare global {
  // eslint-disable-next-line no-var
  var __eventDispatcher: EventDispatcher | undefined;
}

// Next.js's dev bundler (Turbopack) maintains a separate module registry per
// layer (Server Components, Server Actions, instrumentation, ...), so a
// plain module-scope singleton can end up as a different instance per
// layer — the same class of problem Prisma's client hits, solved the same
// way: park the one true instance on `globalThis`, which is shared by the
// whole Node.js process regardless of which layer's module graph asks for it.
export const eventDispatcher = globalThis.__eventDispatcher ?? new EventDispatcher();

if (process.env.NODE_ENV !== "production") {
  globalThis.__eventDispatcher = eventDispatcher;
}
