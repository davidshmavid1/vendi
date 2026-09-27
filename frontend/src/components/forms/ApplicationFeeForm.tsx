"use client";

import { useActionState } from "react";
import { setVendorApplicationFee, type ActionState } from "@/server/actions/organizationSettings";
import { Field, SubmitButton, FormError } from "@/components/ui";

const initialState: ActionState = {};

export function ApplicationFeeForm({
  orgSlug,
  currentFee,
}: {
  orgSlug: string;
  currentFee: number;
}) {
  const [state, formAction, pending] = useActionState(
    setVendorApplicationFee.bind(null, orgSlug),
    initialState,
  );

  return (
    <form action={formAction} className="flex flex-col gap-4">
      <Field
        label="Application fee (cents)"
        name="vendorApplicationFee"
        type="number"
        defaultValue={String(currentFee)}
        placeholder="0"
      />
      <p className="text-xs text-zinc-500">
        Charged to a vendor when they submit an application, regardless of the outcome. Set to 0
        for no fee. Vendi takes no cut of this — it goes straight to you.
      </p>
      <FormError error={state.error} />
      <SubmitButton pending={pending}>Save</SubmitButton>
    </form>
  );
}
