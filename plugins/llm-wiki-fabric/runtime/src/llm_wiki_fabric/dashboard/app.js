/* Dashboard metadata travels only through the same-origin fabric MCP endpoint. */
'use strict';
const $ = id => document.getElementById(id);
let cy, catalog, graph, selected, selectedAgent, protocol = '2025-03-26', sessionId, sequence = 0, loadedAt;

async function rpc(method, params = {}, notification = false) {
  const id = ++sequence;
  const headers = {'Content-Type': 'application/json', Accept: 'application/json, text/event-stream'};
  if (method !== 'initialize') headers['MCP-Protocol-Version'] = protocol;
  if (sessionId) headers['Mcp-Session-Id'] = sessionId;
  const response = await fetch('/mcp', {
    method: 'POST', headers, credentials: 'omit', redirect: 'error',
    signal: AbortSignal.timeout(20000),
    body: JSON.stringify({jsonrpc: '2.0', ...(notification ? {} : {id}), method, params})
  });
  if (!response.ok) throw new Error(`Fabric MCP returned HTTP ${response.status}.`);
  const receivedSession = response.headers.get('Mcp-Session-Id');
  if (receivedSession) sessionId = receivedSession;
  if (notification) return;
  const text = await response.text();
  if (text.length > 2000000) throw new Error('Fabric response exceeds the dashboard limit.');
  let message;
  if ((response.headers.get('Content-Type') || '').includes('text/event-stream')) {
    const events = text.replace(/\r\n/g, '\n').split('\n\n');
    for (const event of events) {
      const value = event.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
      if (value) { const parsed = JSON.parse(value); if (parsed.id === id) message = parsed; }
    }
  } else message = JSON.parse(text);
  if (!message || message.id !== id || message.error) throw new Error('Fabric MCP could not complete the request.');
  return message.result;
}
async function tool(name) {
  const result = await rpc('tools/call', {name, arguments: {}});
  if (result.isError || !result.structuredContent || result.structuredContent.ok === false) throw new Error(`The ${name} MCP tool did not return usable data.`);
  return result.structuredContent;
}
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function freshness(id) {
  const status = {...(catalog.descriptors[id] || {state: 'unknown'})};
  if (Number.isFinite(status.fetched_at)) {
    status.age_seconds = Math.max(0, Math.floor(Date.now() / 1000 - status.fetched_at));
    if (status.age_seconds >= 3600 && ['cached', 'fresh'].includes(status.state)) status.state = 'stale';
  }
  return status;
}
function stateLabel(status) {
  return {local:'Server-local descriptor', fresh:'Fresh descriptor', cached:'Cached descriptor', stale:'Stale descriptor', 'offline-cache':'Offline cache', inline:'Inline descriptor', unknown:'Freshness unknown'}[status.state] || status.state;
}
function field(list, title, value, link = false) {
  const wrap = el('div', undefined, 'field');
  wrap.append(el('dt', title));
  const dd = el('dd');
  if (link && typeof value === 'string' && value.startsWith('https://')) {
    const a = el('a', value); a.href = value; a.target = '_blank'; a.rel = 'noopener noreferrer'; dd.append(a);
  } else dd.textContent = value == null ? 'Not published' : String(value);
  wrap.append(dd); list.append(wrap);
}
function selectResource(id, fit = false) {
  selectedAgent = undefined;
  selected = id;
  for (const card of $('agents').children) card.setAttribute('aria-pressed', 'false');
  if (cy) {
    cy.elements().removeClass('selected');
    const node = cy.nodes().filter(n => n.data('type') === 'resource' && n.data('resource_id') === id);
    node.addClass('selected');
    if (fit && node.length) cy.fit(node.closedNeighborhood(), 60);
  }
  for (const card of $('resources').children) card.setAttribute('aria-pressed', String(card.dataset.id === id));
  renderDetails();
}
function renderDetails() {
  const container = $('details'); container.replaceChildren();
  if (selectedAgent) { renderAgentDetails(container); return; }
  const resource = catalog?.catalog.resources.find(r => r.id === selected);
  if (!resource) { container.append(el('p', 'Select a resource to inspect its descriptor.', 'empty')); return; }
  const status = freshness(resource.id), connection = resource.connection;
  const type = el('div', undefined, 'detail-type'); type.append(el('span', connection.kind === 'mcp' ? 'MCP RESOURCE' : 'POSTGRESQL', `tag ${connection.kind === 'postgresql' ? 'sql' : ''}`));
  container.append(type, el('h3', resource.label), el('p', resource.description, 'description'));
  const badges = el('div', undefined, 'badges');
  badges.append(el('span', stateLabel(status), `badge ${status.state === 'stale' || status.state === 'offline-cache' ? 'warning' : status.state === 'fresh' ? 'fresh' : ''}`), el('span', 'Identity unverified', 'badge warning'));
  container.append(badges, el('h4', 'CONNECTION', 'section-label'));
  const connectionFields = el('dl');
  field(connectionFields, 'Resource ID', resource.id);
  if (connection.kind === 'mcp') { field(connectionFields, 'MCP endpoint', connection.url); field(connectionFields, 'Transport', connection.transport); }
  else {
    field(connectionFields, 'SSH host', connection.access?.host);
    field(connectionFields, 'Database', connection.database);
    field(connectionFields, 'SQL destination on server', connection.host ? `${connection.host}:${connection.port}` : 'Local connection profile required');
    field(connectionFields, 'Access', connection.requires_credentials ? 'Local database credentials and SSH access required' : 'Local connection profile required');
  }
  field(connectionFields, 'Semantic entry point', resource.semantic_entrypoint);
  container.append(connectionFields, el('h4', 'CAPABILITIES', 'section-label'));
  const chips = el('div', undefined, 'chips');
  for (const capability of resource.capabilities) {
    const detail = resource.capability_details?.find(c => c.id === capability);
    const button = el('button', detail?.name || capability); button.type = 'button';
    button.title = detail?.description || capability;
    button.addEventListener('click', () => { $('search').value = capability; applyFilters(); }); chips.append(button);
  }
  container.append(chips, el('h4', 'DESCRIPTOR', 'section-label'));
  const descriptorFields = el('dl');
  field(descriptorFields, 'Source', status.source);
  field(descriptorFields, 'Publisher', resource.publisher_id);
  field(descriptorFields, 'Ontology', resource.ontology_url, true);
  field(descriptorFields, 'Fetched', status.fetched_at ? new Date(status.fetched_at * 1000).toLocaleString() : null);
  if (Number.isFinite(status.age_seconds)) field(descriptorFields, 'Age', `${Math.floor(status.age_seconds / 60)} minutes`);
  container.append(descriptorFields);
  if (status.state === 'local') container.append(el('p', '“Server-local” means the descriptor is maintained on fabric. It does not mean the database runs on your computer.', 'notice'));
  const discovery = catalog.discovery?.[resource.id];
  if (discovery) {
    container.append(el('h4', 'DISCOVERY MODEL', 'section-label'));
    const model = el('dl');
    field(model, 'Fabric ontology', catalog.ontology_profile?.version);
    field(model, 'Graph validation', catalog.ontology_profile?.validation);
    field(model, 'Consumption', discovery.consumption_mode);
    for (const entry of discovery.semantic_entrypoints) field(model, entry.kind, `${entry.tool_name} · ${entry.execution_location}`);
    for (const requirement of discovery.access_requirements) field(model, requirement.mechanism, requirement.target);
    container.append(model, el('p', 'Capabilities are advertised by the resource. They do not grant access or imply that every operation is supported by this client.', 'notice'));
    const services = el('details'); services.append(el('summary', 'Advertised services'));
    const serviceFields = el('dl');
    for (const service of discovery.services) field(serviceFields, `${service.kind}${service.transport ? ' · ' + service.transport : ''}`, service.url || service.binding, Boolean(service.url));
    services.append(serviceFields); container.append(services);
  }
  const details = el('details'); details.append(el('summary', 'Published descriptor & permissions'), el('pre', JSON.stringify(resource, null, 2))); container.append(details);
}
function matches() {
  const query = $('search').value.toLowerCase().trim(), kind = $('kind').value;
  return catalog.catalog.resources.filter(r => (!kind || r.connection.kind === kind) && `${r.id} ${r.label} ${r.description} ${r.capabilities.join(' ')} ${(r.capability_details || []).map(c => [c.name, c.description, ...c.tags].join(' ')).join(' ')}`.toLowerCase().includes(query));
}
function applyFilters() {
  if (!catalog) return;
  const resources = matches(), ids = new Set(resources.map(r => r.id));
  $('result-count').textContent = `${resources.length} of ${catalog.catalog.resources.length}`;
  const list = $('resources'); list.replaceChildren();
  for (const resource of resources) {
    const button = el('button', undefined, 'resource-card'); button.type = 'button'; button.dataset.id = resource.id;
    button.setAttribute('aria-pressed', String(selected === resource.id));
    button.append(el('span', resource.connection.kind === 'mcp' ? 'MCP' : 'POSTGRESQL', `tag ${resource.connection.kind === 'postgresql' ? 'sql' : ''}`), el('span', resource.label, 'resource-name'), el('span', `${resource.capabilities.length} capabilities · ${stateLabel(freshness(resource.id))}`, 'card-meta'));
    button.addEventListener('click', () => selectResource(resource.id, true)); list.append(button);
  }
  if (!resources.length) list.append(el('p', 'No resources match these filters.', 'empty'));
  if (cy) {
    cy.batch(() => {
      cy.elements().removeClass('filtered');
      cy.nodes().filter(n => n.data('type') !== 'agent' && !ids.has(n.data('resource_id'))).addClass('filtered');
      cy.edges().filter(e => e.source().hasClass('filtered') || e.target().hasClass('filtered')).addClass('filtered');
    });
    if (resources.length) cy.fit(cy.elements().not('.filtered'), 55);
  }
  $('graph-message').hidden = resources.length > 0 || (graph.activity?.agents.length || 0) > 0;
  $('graph-message').textContent = 'No resources match these filters.';
  if (!ids.has(selected)) selected = resources[0]?.id;
  if (selectedAgent) selectAgent(selectedAgent); else selectResource(selected);
}
function renderGraph() {
  if (cy) cy.destroy();
  cy = cytoscape({container: $('graph'), elements: [...graph.nodes, ...graph.edges], minZoom: .15, maxZoom: 3, wheelSensitivity: .2,
    style: [
      {selector:'node', style:{label:'data(label)', 'font-family':'system-ui, sans-serif', 'font-size':13, color:'#2b5364', 'text-wrap':'wrap', 'text-max-width':130, 'text-valign':'center', 'text-halign':'center', width:105, height:38, 'background-color':'#e0f2ef', 'border-color':'#8fc9c1', 'border-width':1.5, shape:'round-rectangle'}},
      {selector:'node[type="resource"]', style:{width:170, height:64, 'background-color':'#285ddd', color:'#fff', 'border-color':'#1d49b3', 'font-size':15, 'font-weight':600, 'text-max-width':150}},
      {selector:'node[type="service"], node[type="semantic_entrypoint"], node[type="access_requirement"]', style:{'background-color':'#eef1f7', 'border-color':'#a9b6c8', color:'#3b526d', shape:'round-rectangle', 'font-size':11, width:110}},
      {selector:'node[type="agent"]', style:{shape:'ellipse', width:150, height:70, 'background-color':'#7155ac', color:'#fff', 'border-color':'#563b90'}},
      {selector:'edge', style:{width:1.4, 'line-color':'#b2c4d9', 'target-arrow-shape':'triangle', 'target-arrow-color':'#b2c4d9', 'arrow-scale':.7, 'curve-style':'bezier'}},
      {selector:'edge[type="discovered"]', style:{'line-style':'dashed', 'line-color':'#957cba', 'target-arrow-color':'#957cba', label:'discovered', 'font-size':10, 'text-background-color':'#fff', 'text-background-opacity':.85}},
      {selector:'.selected', style:{'border-width':4, 'border-color':'#90b4ff'}},
      {selector:'.filtered', style:{display:'none'}}
    ],
    layout:{name:'cose', animate:false, nodeDimensionsIncludeLabels:true, nodeRepulsion:() => 16000, idealEdgeLength:() => 110, componentSpacing:100, padding:55, numIter:1000}
  });
  cy.on('tap', 'node', event => event.target.data('type') === 'agent' ? selectAgent(event.target.data('agent_id')) : selectResource(event.target.data('resource_id')));
}
function selectAgent(id) {
  selectedAgent = id; selected = undefined;
  if (cy) {
    cy.elements().removeClass('selected');
    cy.nodes().filter(n => n.data('agent_id') === id).addClass('selected');
  }
  for (const card of $('resources').children) card.setAttribute('aria-pressed', 'false');
  for (const card of $('agents').children) card.setAttribute('aria-pressed', String(card.dataset.id === id));
  renderDetails();
}
function renderAgentDetails(container) {
  const agent = graph?.activity?.agents.find(a => a.id === selectedAgent);
  if (!agent) { container.append(el('p', 'This announcement has expired. Select another node.', 'empty')); return; }
  container.append(el('span', 'PROJECT AGENT', 'tag'), el('h3', agent.id));
  if (agent.metadata?.description) container.append(el('p', agent.metadata.description, 'description'));
  const fields = el('dl');
  field(fields, 'Federation membership at announcement', agent.federation_membership);
  field(fields, 'Caller identity', 'Unverified');
  field(fields, 'Card', agent.card_url, true);
  field(fields, 'Index source', agent.federation.source, true);
  field(fields, 'Index freshness at view load', agent.federation.state);
  field(fields, 'Last seen', new Date(agent.last_seen * 1000).toLocaleString());
  field(fields, 'Sessions retained', agent.sessions.length);
  container.append(fields);
  if (agent.metadata?.capabilities?.length) {
    container.append(el('h4', 'DECLARED CAPABILITIES', 'section-label'));
    const list = el('ul'); for (const capability of agent.metadata.capabilities) list.append(el('li', capability)); container.append(list);
  }
  container.append(el('h4', 'RECENT SESSIONS', 'section-label'));
  for (const session of agent.sessions) {
    const recent = Date.now()/1000 - session.last_seen < graph.activity.recent_window_seconds;
    container.append(el('p', `${session.client || 'Unspecified client'} · ${recent ? 'Seen within 15 minutes' : 'Not recently seen'} · ${new Date(session.last_seen * 1000).toLocaleString()}`, 'notice'));
  }
  container.append(el('p', 'Dashed edges record resources returned by discovery to a session announcing this identity. They do not establish direct queries or authenticate that session.', 'notice'));
}
function renderAgents() {
  const agents = graph.activity?.agents || [];
  $('agent-count').textContent = agents.length;
  $('agent-status').textContent = 'Last 7 days · unaffected by resource filters';
  const list = $('agents'); list.replaceChildren();
  for (const agent of agents) {
    const button = el('button', undefined, 'resource-card'); button.type = 'button'; button.dataset.id = agent.id;
    button.setAttribute('aria-pressed', String(selectedAgent === agent.id));
    button.append(el('span', 'AGENT', 'tag agent'), el('span', agent.id, 'resource-name'), el('span', `${agent.sessions.length} sessions · last seen ${new Date(agent.last_seen * 1000).toLocaleString()}`, 'card-meta'));
    button.addEventListener('click', () => selectAgent(agent.id)); list.append(button);
  }
  if (!agents.length) list.append(el('p', 'No agents have announced within the retention window.', 'empty'));
}
async function load() {
  $('refresh').disabled = true; $('error').hidden = true;
  $('connection-status').textContent = 'Loading from MCP…';
  try {
    sessionId = undefined;
    const initialized = await rpc('initialize', {protocolVersion:'2025-03-26', capabilities:{}, clientInfo:{name:'fabric-dashboard', version:'1.0.0'}});
    protocol = initialized.protocolVersion;
    await rpc('notifications/initialized', {}, true);
    const [nextGraph, nextCatalog] = await Promise.all([tool('fabric_graph'), tool('fabric_catalog')]);
    if (nextGraph.revision !== nextCatalog.revision) throw new Error('Catalog changed while loading. Refresh to obtain a consistent view.');
    if (!Array.isArray(nextGraph.nodes) || !Array.isArray(nextGraph.edges) || !Array.isArray(nextCatalog.catalog?.resources)) throw new Error('The published graph or catalog is invalid.');
    graph = nextGraph; catalog = nextCatalog; loadedAt = new Date();
    $('resource-count').textContent = catalog.catalog.resources.length;
    $('capability-count').textContent = graph.nodes.filter(n => n.data.type === 'capability').length;
    $('revision').textContent = graph.revision.slice(0,8); $('revision').title = graph.revision;
    renderGraph(); renderAgents(); applyFilters();
    $('connection-status').textContent = 'View loaded';
    $('loaded-at').textContent = `Loaded ${loadedAt.toLocaleTimeString()}`;
  } catch (error) {
    $('error').textContent = `${error.message} ${catalog ? 'Showing the previously loaded view.' : 'No resource data has been loaded.'}`;
    $('error').hidden = false; $('connection-status').textContent = 'Refresh failed';
    if (!catalog) { $('graph-message').hidden = false; $('graph-message').textContent = 'Unable to load the graph. Use Refresh view to retry.'; }
  } finally { $('refresh').disabled = false; }
}
$('refresh').addEventListener('click', load);
$('filters').addEventListener('submit', event => event.preventDefault());
$('search').addEventListener('input', applyFilters); $('kind').addEventListener('change', applyFilters);
$('clear').addEventListener('click', () => { $('search').value = ''; $('kind').value = ''; applyFilters(); });
$('fit').addEventListener('click', () => cy?.fit(cy.elements().not('.filtered'), 55));
function zoom(factor) { if (cy) cy.zoom({level:cy.zoom() * factor, renderedPosition:{x:cy.width()/2,y:cy.height()/2}}); }
$('zoom-in').addEventListener('click', () => zoom(1.25)); $('zoom-out').addEventListener('click', () => zoom(.8));
window.addEventListener('resize', () => { if (cy) {cy.resize(); cy.fit(cy.elements().not('.filtered'),55);} });
setInterval(() => { if (catalog) {renderDetails();} }, 60000);
load();
