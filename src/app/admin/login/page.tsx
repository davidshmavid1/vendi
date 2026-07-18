"use client";

import { useActionState } from "react";
import { loginAdmin, type LoginState } from "@/server/actions/auth";
import { Card, Field, SubmitButton, FormError } from "@/components/ui";

const initialState: LoginState = {};

export default function AdminLoginPage() {
  const [state, formAction, pending] = useActionState(loginAdmin, initialState);

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <h1 className="text-2xl font-semibold">Platform admin sign in</h1>
      <Card className="mt-6">
        <form action={formAction} className="flex flex-col gap-4">
          <Field label="Email" name="email" type="email" />
          <Field label="Password" name="password" type="password" />
          <FormError error={state.error} />
          <SubmitButton pending={pending}>Sign in</SubmitButton>
        </form>
      </Card>
    </div>
  );
}
