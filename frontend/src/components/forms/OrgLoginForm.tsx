"use client";

import { useActionState } from "react";
import { loginOrg, type LoginState } from "@/server/actions/auth";
import { Card, Field, SubmitButton, FormError } from "@/components/ui";

const initialState: LoginState = {};

export function OrgLoginForm({ orgSlug }: { orgSlug: string }) {
  const [state, formAction, pending] = useActionState(loginOrg.bind(null, orgSlug), initialState);

  return (
    <Card>
      <form action={formAction} className="flex flex-col gap-4">
        <Field label="Email" name="email" type="email" />
        <Field label="Password" name="password" type="password" />
        <FormError error={state.error} />
        <SubmitButton pending={pending}>Sign in</SubmitButton>
      </form>
    </Card>
  );
}
