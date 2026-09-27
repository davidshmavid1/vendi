import { VendorSignupForm } from "@/components/forms/VendorSignupForm";

export default async function VendorSignupPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <h1 className="text-2xl font-semibold">Become a vendor</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Create your account, then apply to open events.
      </p>
      <div className="mt-6">
        <VendorSignupForm orgSlug={orgSlug} />
      </div>
    </div>
  );
}
