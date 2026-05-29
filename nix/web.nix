# nix/web.nix — Tribal Web Dashboard (Vite/React) frontend build
{ pkgs, tribalNpmLib, ... }:
let
  src = ../web;
  npmDeps = pkgs.fetchNpmDeps {
    inherit src;
    hash = "sha256-CmGZlAYLKRyNCjuHfRIzzbAdbm1ng7+owW8vMFciz5g=";
  };

  npm = tribalNpmLib.mkNpmPassthru { folder = "web"; attr = "web"; pname = "tribal-web"; };

  packageJson = builtins.fromJSON (builtins.readFile (src + "/package.json"));
  version = packageJson.version;
in
pkgs.buildNpmPackage (npm // {
  pname = "tribal-web";
  inherit src npmDeps version;

  doCheck = false;

  buildPhase = ''
    npx tsc -b
    npx vite build --outDir dist
  '';

  installPhase = ''
    runHook preInstall
    cp -r dist $out
    runHook postInstall
  '';
})
