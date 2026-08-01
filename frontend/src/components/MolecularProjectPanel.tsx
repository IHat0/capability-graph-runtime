import type { LoadedMolecularProjectScene } from '../scene/native-project'

function Rows({ rows }: { rows: Array<[string, string | number]> }) {
  return <dl className="property-list">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
}

export function MolecularProjectPanel({ scene }: { scene: LoadedMolecularProjectScene }) {
  const totalAtoms = scene.metadata.structures.reduce((sum, structure) => sum + structure.atom_count, 0)
  return <aside className="science-panel" aria-label="Molecular project inspector">
    <section className="inspector-section">
      <p className="section-kicker">Read-only molecular project</p>
      <h1>{scene.metadata.project_identifier}</h1>
      <Rows rows={[
        ['Scene', scene.metadata.scene_identifier],
        ['Structures', scene.structures.length],
        ['Declared atoms', totalAtoms],
        ['Primary structure', scene.metadata.primary_structure_identifier],
        ['Native formats', scene.metadata.structures.map((item) => item.native_format).join(', ')],
      ]} />
    </section>
    <section className="inspector-section">
      <p className="section-kicker">Declared structure identities</p>
      <h2>Native structures</h2>
      <Rows rows={scene.metadata.structures.map((structure) => [structure.structure_identifier, `${structure.native_format} - ${structure.atom_count} atoms`])} />
    </section>
    <section className="inspector-section">
      <p className="section-kicker">Project annotations</p>
      <h2>Components, selections, and regions</h2>
      <Rows rows={[
        ['Components', scene.metadata.components.map((item) => item.component_identifier).join(', ') || 'None declared'],
        ['Selections', scene.metadata.selections.map((item) => item.selection_identifier).join(', ') || 'None declared'],
        ['Regions', scene.metadata.regions.map((item) => item.region_identifier).join(', ') || 'None declared'],
      ]} />
    </section>
    <section className="inspector-section">
      <p className="section-kicker">Immutable artifacts</p>
      <h2>Verified browser resources</h2>
      <Rows rows={scene.metadata.artifact_references.map((artifact) => [artifact.artifact_identifier, artifact.content_sha256])} />
    </section>
    <section className="execution-control execution-control--read-only">
      <strong>Read-only project scene</strong>
      <p>Opening this scene did not create, resume, or execute a scientific run.</p>
    </section>
  </aside>
}
