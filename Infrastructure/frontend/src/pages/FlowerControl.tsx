import ZoneConfig from './ZoneConfig'

/** Single Flower control surface: equipment lives under cluster `main` in config. */
export default function FlowerControl() {
  return <ZoneConfig location="Flower Room" cluster="main" />
}
