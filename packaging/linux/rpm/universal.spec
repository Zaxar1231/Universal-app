# Пакет для Fedora (и других RPM-систем). Собирается в CI:
# rpmbuild -bb --define "_sourcedir <папка с tar.gz>" --define "ver <v>" universal.spec
Name:           universal
Version:        %{ver}
Release:        1
Summary:        File converter, video editor, video downloader, PDF tools and QR codes
License:        AGPL-3.0-only
URL:            https://github.com/Zaxar1231/Universal-app
Source0:        universal-%{version}-linux.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       webkit2gtk4.1
Requires:       python3-pillow
Requires:       python3-cryptography
Requires:       (ffmpeg or ffmpeg-free)
Requires:       ImageMagick
Requires:       pandoc
Requires:       poppler-utils
Recommends:     libreoffice-writer
Recommends:     ocrmypdf
Recommends:     tesseract-langpack-rus
Recommends:     python3-py7zr
%global __requires_exclude_from ^/usr/lib/universal/.*$
%global __provides_exclude_from ^/usr/lib/universal/.*$
%global debug_package %{nil}
%global __brp_python_bytecompile %{nil}

%description
Universal converts images, audio, video and documents (like vert.sh),
edits video and audio with ffmpeg, downloads videos with yt-dlp,
edits PDFs and makes QR codes. Everything runs locally in its own window.

%prep

%build

%install
mkdir -p %{buildroot}
tar -C %{buildroot} -xzf %{SOURCE0}

%files
/usr/bin/universal
/usr/lib/universal
/usr/share/applications/universal.desktop
/usr/share/icons/hicolor/*/apps/universal.png
/usr/share/doc/universal
