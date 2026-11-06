function decorateNavigationIcons(root) {
  const shapes={
    'materials':'<g class="icon-motion"><rect x="3" y="3" width="14" height="14" rx="3"/></g><rect x="7" y="7" width="14" height="14" rx="3" fill="var(--icon-face,#fff)"/><path d="M10 12h8m-8 4h5"/>',
    'timetable':'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 10h18"/><g class="icon-motion"><path d="M7 14h3m4 0h3m-10 4h3"/></g>',
    'teachers':'<g class="icon-motion"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="M5.5 16c.5-4 6.5-4 7 0M15 9h3m-3 4h3"/></g>',
    'exams':'<rect x="5" y="4" width="15" height="17" rx="3"/><path d="M9 3h7v3H9zM13 11h4m-4 5h4"/><path class="icon-motion" d="m7 11 1 1 2-2m-3 6 1 1 2-2"/>',
    'reminders':'<g class="icon-motion"><path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zM10 21h4M12 2v2"/></g>',
    'mail':'<rect x="3" y="5" width="18" height="14" rx="3"/><path class="icon-motion" d="m4 7 8 6 8-6"/>',
    'files':'<path d="M3 8V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9"/><path class="icon-motion" d="M3 9h18l-2 11H5z"/>'
  };
  for(const [key,shape] of Object.entries(shapes)){
    const button=root.querySelector('#nav-'+key);
    if(!button||button.querySelector('.nav-icon'))continue;
    const holder=document.createElement('span');holder.className='nav-icon nav-icon-'+key;
    holder.setAttribute('aria-hidden','true');
    holder.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" focusable="false">'+shape+'</svg>';
    button.prepend(holder);
  }
}
