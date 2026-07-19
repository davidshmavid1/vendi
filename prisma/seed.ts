import "dotenv/config";
import { PrismaClient } from "../src/generated/prisma/client";
import { PrismaPg } from "@prisma/adapter-pg";
import bcrypt from "bcryptjs";

const adapter = new PrismaPg({ connectionString: process.env.DATABASE_URL });
const prisma = new PrismaClient({ adapter });

const SEED_SLUG = "maple-street-market";
const PASSWORD = "password123";

async function main() {
  const existing = await prisma.organization.findUnique({ where: { slug: SEED_SLUG } });
  if (existing) {
    console.log(`Organization "${SEED_SLUG}" already exists — skipping seed.`);
    return;
  }

  const hashedPassword = await bcrypt.hash(PASSWORD, 12);

  const existingAdmin = await prisma.user.findFirst({
    where: { email: "admin@vendi.dev", role: "PLATFORM_ADMIN" },
  });
  if (!existingAdmin) {
    await prisma.user.create({
      data: { email: "admin@vendi.dev", name: "Vendi Admin", hashedPassword, role: "PLATFORM_ADMIN" },
    });
  }

  const organization = await prisma.organization.create({
    data: {
      name: "Maple Street Market",
      slug: SEED_SLUG,
      applicationFeeBps: 100,
      vendorApplicationFee: 500, // $5 to apply, for demoing the new flow
      // Seeded as ACTIVE, bypassing real Checkout, so local testing isn't
      // blocked behind a live subscription flow every time.
      subscriptionStatus: "ACTIVE",
    },
  });

  const organizer = await prisma.user.create({
    data: {
      organizationId: organization.id,
      email: "organizer@maplestreet.test",
      name: "Jamie Organizer",
      hashedPassword,
      role: "ORGANIZER",
    },
  });

  const event = await prisma.event.create({
    data: {
      organizationId: organization.id,
      name: "Spring Night Market",
      description: "A monthly evening popup market downtown.",
      location: "Maple Street Plaza",
      startDate: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000),
      endDate: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000 + 6 * 60 * 60 * 1000),
      status: "APPLICATIONS_OPEN",
    },
  });

  const smallSpace = await prisma.space.create({
    data: {
      organizationId: organization.id,
      eventId: event.id,
      name: "10x10 booth",
      price: 7500,
      totalQty: 15,
      availableQty: 15,
    },
  });

  await prisma.space.create({
    data: {
      organizationId: organization.id,
      eventId: event.id,
      name: "10x20 corner booth",
      price: 15000,
      totalQty: 5,
      availableQty: 5,
    },
  });

  const vendorAUser = await prisma.user.create({
    data: {
      organizationId: organization.id,
      email: "sunny@vendor.test",
      name: "Sunny Rivera",
      hashedPassword,
      role: "VENDOR",
      vendor: {
        create: {
          organizationId: organization.id,
          businessName: "Sunny Side Bakes",
          businessInfo: { category: "Baked goods" },
        },
      },
    },
    include: { vendor: true },
  });

  const vendorBUser = await prisma.user.create({
    data: {
      organizationId: organization.id,
      email: "clay@vendor.test",
      name: "Clay Nguyen",
      hashedPassword,
      role: "VENDOR",
      vendor: {
        create: {
          organizationId: organization.id,
          businessName: "Clay & Co. Ceramics",
          businessInfo: { category: "Handmade goods" },
        },
      },
    },
    include: { vendor: true },
  });

  await prisma.application.create({
    data: {
      organizationId: organization.id,
      vendorId: vendorAUser.vendor!.id,
      eventId: event.id,
      status: "APPROVED",
      reviewedAt: new Date(),
      reviewedBy: organizer.id,
    },
  });

  await prisma.application.create({
    data: {
      organizationId: organization.id,
      vendorId: vendorBUser.vendor!.id,
      eventId: event.id,
      status: "SUBMITTED",
    },
  });

  console.log("Seed complete:\n");
  console.log(`  Organizer:      organizer@maplestreet.test / ${PASSWORD}  →  /o/${SEED_SLUG}/login`);
  console.log(`  Vendor (approved, ready to book "${smallSpace.name}"): sunny@vendor.test / ${PASSWORD}`);
  console.log(`  Vendor (pending review):                                clay@vendor.test / ${PASSWORD}`);
  console.log(`  Platform admin: admin@vendi.dev / ${PASSWORD}  →  /admin/login`);
  console.log(`\nNote: connect Stripe from the organizer's dashboard before trying to book a space.`);
  console.log(`Applying to "${event.name}" costs $5 (vendorApplicationFee) — 0% cut.`);
}

main()
  .catch((err) => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
