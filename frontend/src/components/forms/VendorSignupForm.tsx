"use client";

import { useActionState } from "react";
import { signupVendor, type VendorSignupState } from "@/server/actions/vendors";
import { Card, Field, SubmitButton, FormError } from "@/components/ui";

const initialState: VendorSignupState = {};

export function VendorSignupForm({ orgSlug }: { orgSlug: string }) {
  const [state, formAction, pending] = useActionState(signupVendor, initialState);

  return (
    <Card>
      <form action={formAction} className="flex flex-col gap-4">
        <input type="hidden" name="orgSlug" value={orgSlug} />
        <Field label="Business name" name="businessName" placeholder="Sunny Side Bakes" />
        <Field label="Your name" name="name" />
        <Field label="Email" name="email" type="email" />
        <Field label="Password" name="password" type="password" />
        <FormError error={state.error} />
        <SubmitButton pending={pending}>Create vendor account</SubmitButton>
      </form>
    </Card>
  );
}
