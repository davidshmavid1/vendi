"use client";

import { useActionState } from "react";
import { createSpace, type ActionState } from "@/server/actions/spaces";
import { Card, Field, TextArea, SubmitButton, FormError } from "@/components/ui";

const initialState: ActionState = {};

export function CreateSpaceForm({ orgSlug, eventId }: { orgSlug: string; eventId: string }) {
  const [state, formAction, pending] = useActionState(createSpace.bind(null, orgSlug), initialState);

  return (
    <Card>
      <form action={formAction} className="flex flex-col gap-4">
        <input type="hidden" name="eventId" value={eventId} />
        <Field label="Space name" name="name" placeholder="10x10 booth" />
        <TextArea label="Description" name="description" />
        <div className="grid grid-cols-2 gap-4">
          <Field label="Price (cents)" name="price" type="number" placeholder="7500" />
          <Field label="Quantity" name="totalQty" type="number" placeholder="10" />
        </div>
        <FormError error={state.error} />
        <SubmitButton pending={pending}>Add space</SubmitButton>
      </form>
    </Card>
  );
}
