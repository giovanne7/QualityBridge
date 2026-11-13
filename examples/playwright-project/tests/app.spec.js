const { test, expect } = require('@playwright/test');

test('página inicial @smoke', async ({ page }) => {
  await page.setContent('<main><h1>QualityBridge</h1><p>Testes automatizados</p></main>');
  await expect(page.getByRole('heading', { name: 'QualityBridge' })).toBeVisible();
});

test('interação com formulário', async ({ page }) => {
  await page.setContent('<label>Nome <input aria-label="Nome"></label>');
  await page.getByRole('textbox', { name: 'Nome' }).fill('Equipe de TI');
  await expect(page.getByRole('textbox', { name: 'Nome' })).toHaveValue('Equipe de TI');
});
