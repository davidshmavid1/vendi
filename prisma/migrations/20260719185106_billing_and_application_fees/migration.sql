-- AlterEnum
ALTER TYPE "ApplicationStatus" ADD VALUE 'PENDING_FEE_PAYMENT';

-- CreateEnum
CREATE TYPE "PaymentPurpose" AS ENUM ('BOOKING_FEE', 'APPLICATION_FEE');

-- CreateEnum
CREATE TYPE "SubscriptionStatus" AS ENUM ('NONE', 'ACTIVE', 'PAST_DUE', 'CANCELED');

-- AlterTable
ALTER TABLE "Organization"
  ALTER COLUMN "applicationFeeBps" SET DEFAULT 100,
  ADD COLUMN "vendorApplicationFee" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN "stripeCustomerId" TEXT,
  ADD COLUMN "subscriptionStatus" "SubscriptionStatus" NOT NULL DEFAULT 'NONE',
  ADD COLUMN "stripeSubscriptionId" TEXT,
  ADD COLUMN "subscriptionCurrentPeriodEnd" TIMESTAMP(3);

-- AlterTable
ALTER TABLE "Payment"
  ADD COLUMN "purpose" "PaymentPurpose" NOT NULL DEFAULT 'BOOKING_FEE',
  ADD COLUMN "applicationId" TEXT,
  ALTER COLUMN "bookingId" DROP NOT NULL;

ALTER TABLE "Payment" ALTER COLUMN "purpose" DROP DEFAULT;

-- AddForeignKey
ALTER TABLE "Payment" ADD CONSTRAINT "Payment_applicationId_fkey"
  FOREIGN KEY ("applicationId") REFERENCES "Application"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- CreateIndex
CREATE INDEX "Payment_organizationId_applicationId_idx" ON "Payment"("organizationId", "applicationId");
