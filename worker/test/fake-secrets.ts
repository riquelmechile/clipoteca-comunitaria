export function fakeSecret(label: string, length = 32): string {
  if (length < 16) throw new RangeError("test credential length must be at least 16");
  const seed = ["fake", label, "value"].join("-");
  return (seed + "x".repeat(length)).slice(0, length);
}
