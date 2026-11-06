import { activationCode, type Sim } from '../api/sims';
import { QrCode } from '../ui/QrCode';

/** An eSIM's activation code as text and as a QR for the phone's camera. The server sends
 * it only while the eSIM is reserved or active; a stocked profile shows none. */
export function SimActivationCode({ sim }: { sim: Sim }) {
  const code = activationCode(sim);
  if (code === undefined) return null;
  return (
    <div className="bss-modal-body" data-ui="ui.sims.activationCode">
      <p>
        Activation code: <code>{code}</code>
      </p>
      <QrCode text={code} label={`Activation code QR for ${sim.value ?? sim.id}`} />
      <p className="bss-caption">
        Anyone with this code can install the profile: share it with the subscriber only.
      </p>
    </div>
  );
}
