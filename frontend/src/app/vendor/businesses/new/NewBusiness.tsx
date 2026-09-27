"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Card, FormError } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, inputClass, Notice } from "@/components/DjangoPage";
import { apiSend } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { CATEGORIES } from "@/lib/applications/types";

// Creates a shared vendor business profile (you become its owner). It isn't
// tied to any organization or market.
export function NewBusiness({ next }: { next: string }) {
  const router = useRouter();
  const { state: account, reload } = useAccount();
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const value = (name: string) => String(form.get(name) ?? "").trim();
    setPending(true);
    setError(undefined);
    const result = await apiSend("POST", "/vendors", {
      name: value("name"),
      category: value("category"),
      contact_email: value("contact_email"),
      description: value("description"),
      city: value("city"),
      region: value("region"),
    });
    if (result.ok) {
      router.replace(next);
      return;
    }
    setPending(false);
    setError(result.error.message);
  }

  return (
    <DjangoPage account={account}>
      <h1 className="text-2xl font-semibold tracking-tight">Create a vendor business</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Your business profile is yours: you can use it to apply to any market on Vendi.
      </p>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {account.status === "signed_in" && !account.account.email_verified ? (
            <Notice tone="amber">Confirm your email address before creating a business.</Notice>
          ) : (
            <Card>
              <form onSubmit={submit} className="flex flex-col gap-4">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">Business name</span>
                  <input name="name" required maxLength={120} className={inputClass} />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">Category</span>
                  <select name="category" required className={inputClass} defaultValue="">
                    <option value="" disabled>
                      Choose a category
                    </option>
                    {Object.entries(CATEGORIES).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">Business contact email</span>
                  <input name="contact_email" type="email" required maxLength={254} className={inputClass} />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">Description (optional)</span>
                  <textarea name="description" rows={3} maxLength={2000} className={inputClass} />
                </label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="font-medium">City (optional)</span>
                    <input name="city" maxLength={100} className={inputClass} />
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="font-medium">State or region (optional)</span>
                    <input name="region" maxLength={100} className={inputClass} />
                  </label>
                </div>
                <div role="alert">
                  <FormError error={error} />
                </div>
                <button type="submit" disabled={pending} className={buttonClass}>
                  {pending ? "Creating…" : "Create business"}
                </button>
              </form>
            </Card>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
