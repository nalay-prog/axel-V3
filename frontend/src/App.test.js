import { render, screen } from '@testing-library/react';
import App from './App';

test('renders darwin interface', () => {
  render(<App />);
  const brandElements = screen.getAllByText(/darwin/i);
  expect(brandElements.length).toBeGreaterThan(0);
});
