const inrFormatter = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatInr(amount: number): string {
  return `INR ${inrFormatter.format(amount)}`;
}
