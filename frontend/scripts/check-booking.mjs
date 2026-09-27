import "dotenv/config";
import { PrismaClient } from "../src/generated/prisma/client.ts";
import { PrismaPg } from "@prisma/adapter-pg";

const adapter = new PrismaPg({ connectionString: process.env.DATABASE_URL });
const prisma = new PrismaClient({ adapter });

const [, , bookingId] = process.argv;

const booking = await prisma.booking.findUnique({
  where: { id: bookingId },
  include: { payments: true, space: true },
});
console.log(JSON.stringify(booking, null, 2));
await prisma.$disconnect();
