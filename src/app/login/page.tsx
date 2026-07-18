import Link from "next/link";

export default function GenericLoginPage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-24 text-center">
      <h1 className="text-2xl font-semibold">Sign in through your market</h1>
      <p className="mt-2 max-w-md text-zinc-600 dark:text-zinc-400">
        Vendi sign-in is per market. Use the login link your organizer gave you (it looks like
        <code className="mx-1 rounded bg-zinc-100 px-1.5 py-0.5 text-sm dark:bg-zinc-800">
          /o/your-market/login
        </code>
        ), or start a new market below.
      </p>
      <Link
        href="/signup"
        className="mt-6 rounded-md bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200"
      >
        Start your market
      </Link>
    </div>
  );
}
