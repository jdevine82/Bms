// Signing in for the browser tests (milestone 6A). On a fresh database, opened from the server
// itself, the first-run page creates the test admin; otherwise the login page is used.
//   BMS_USER / BMS_PASSWORD (default: testadmin / a test-only password).
export const TEST_USER = process.env.BMS_USER ?? "testadmin";
export const TEST_PASSWORD = process.env.BMS_PASSWORD ?? "test-only password 1";

export async function signIn(page, url) {
  await page.goto(url);
  const firstRun = page.getByRole("heading", { name: "Create the first admin" });
  const login = page.getByRole("button", { name: "Log in" });
  await Promise.race([firstRun.waitFor({ timeout: 15000 }), login.waitFor({ timeout: 15000 })]);
  if (await firstRun.isVisible()) {
    await page.getByLabel("Your name").fill("Test admin");
    await page.getByLabel("User name").fill(TEST_USER);
    await page.getByLabel("Password (at least 10 characters)").fill(TEST_PASSWORD);
    await page.getByLabel("Password again").fill(TEST_PASSWORD);
    await page.getByRole("button", { name: "Create the admin account" }).click();
  } else {
    await page.getByLabel("User name").fill(TEST_USER);
    await page.getByLabel("Password", { exact: true }).fill(TEST_PASSWORD);
    await login.click();
    // Logins are rate-limited per address (10 a minute, milestone 6A); test runs back to back
    // from one machine can reach it, so wait it out once.
    const limited = page.getByText(/Too many login attempts/);
    const done = page.getByRole("button", { name: "Sign out", exact: true });
    await Promise.race([limited.waitFor({ timeout: 15000 }), done.waitFor({ timeout: 15000 })]);
    if (await limited.isVisible()) {
      await page.waitForTimeout(61000);
      await login.click();
    }
  }
  await page.getByRole("button", { name: "Sign out", exact: true }).waitFor({ timeout: 15000 });
}
