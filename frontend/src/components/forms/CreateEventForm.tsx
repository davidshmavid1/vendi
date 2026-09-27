"use client";

import { useActionState } from "react";
import { createEvent, type ActionState } from "@/server/actions/events";
import { Card, Field, TextArea, SubmitButton, FormError } from "@/components/ui";

const initialState: ActionState = {};

export function CreateEventForm({ orgSlug }: { orgSlug: string }) {
  const [state, formAction, pending] = useActionState(createEvent.bind(null, orgSlug), initialState);

  return (
    <Card>
      <form action={formAction} className="flex flex-col gap-4">
        <Field label="Event name" name="name" placeholder="Spring Night Market" />
        <TextArea label="Description" name="description" />
        <Field label="Location" name="location" required={false} placeholder="Downtown Plaza" />
        <div className="grid grid-cols-2 gap-4">
          <Field label="Start date" name="startDate" type="date" />
          <Field label="End date" name="endDate" type="date" />
        </div>
        <FormError error={state.error} />
        <SubmitButton pending={pending}>Create event</SubmitButton>
      </form>
    </Card>
  );
}
