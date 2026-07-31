import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Sidepanel } from './Sidepanel';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Sidepanel />
  </StrictMode>,
);
