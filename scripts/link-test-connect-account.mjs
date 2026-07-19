import "dotenv/config";
import { PrismaClient } from "../src/generated/prisma/client.ts";
import { PrismaPg } from "@prisma/adapter-pg";

const adapter = new PrismaPg({ connectionString: process.env.DATABASE_URL });
const prisma = new PrismaClient({ adapter });

const [, , orgSlug, accountId] = process.argv;
if (!orgSlug || !accountId) {
  console.error("Usage: node scripts/link-test-connect-account.mjs <orgSlug> <acct_id>");
  process.exit(1);
}

const organization = await prisma.organization.update({
  where: { slug: orgSlug },
  data: { stripeConnectAccountId: accountId, stripeOnboardingComplete: true },
});

console.log(`Linked ${organization.slug} to ${accountId} (test-only, bypasses hosted onboarding)`);
await prisma.$disconnect();
