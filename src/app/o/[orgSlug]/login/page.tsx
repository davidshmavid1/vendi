import { OrgLoginForm } from "@/components/forms/OrgLoginForm";

export default async function OrgLoginPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <h1 className="text-2xl font-semibold">Sign in</h1>
      <div className="mt-6">
        <OrgLoginForm orgSlug={orgSlug} />
      </div>
    </div>
  );
}
