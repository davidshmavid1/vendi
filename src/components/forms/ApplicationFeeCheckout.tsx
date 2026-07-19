"use client";

import { useCallback, useState, type FormEvent } from "react";
import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { getStripeClient } from "@/lib/stripeClient";
import { initiateApplicationFeePaymentAction } from "@/server/actions/applications";
import { SubmitButton, FormError } from "@/components/ui";

export function ApplicationFeeCheckout({
  orgSlug,
  eventId,
  fee,
}: {
  orgSlug: string;
  eventId: string;
  fee: number;
}) {
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);

  const start = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    const result = await initiateApplicationFeePaymentAction(orgSlug, eventId);
    setLoading(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setClientSecret(result.clientSecret);
  }, [orgSlug, eventId]);

  if (!clientSecret) {
    return (
      <div className="flex flex-col gap-3">
        <FormError error={error} />
        <button
          onClick={start}
          disabled={loading}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-zinc-900"
        >
          {loading ? "Starting…" : `Pay $${(fee / 100).toFixed(2)} to apply`}
        </button>
      </div>
    );
  }

  return (
    <Elements stripe={getStripeClient()} options={{ clientSecret }}>
      <PaymentForm orgSlug={orgSlug} />
    </Elements>
  );
}

function PaymentForm({ orgSlug }: { orgSlug: string }) {
  const stripe = useStripe();
  const elements = useElements();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | undefined>();

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!stripe || !elements) return;

    setSubmitting(true);
    setError(undefined);

    const { error: confirmError } = await stripe.confirmPayment({
      elements,
      confirmParams: {
        return_url: `${window.location.origin}/o/${orgSlug}/vendor/dashboard`,
      },
    });

    if (confirmError) {
      setError(confirmError.message ?? "Payment failed");
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <PaymentElement />
      <FormError error={error} />
      <SubmitButton pending={submitting || !stripe}>Pay now</SubmitButton>
    </form>
  );
}
