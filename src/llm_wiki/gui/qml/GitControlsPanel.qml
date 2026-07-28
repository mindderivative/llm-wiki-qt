import QtQuick
import QtQuick.Controls

Item {
    property string title: qsTr("Git Controls")

    Label {
        anchors.centerIn: parent
        text: qsTr("Git status/stage/commit -- wired up in Phase 15b")
        opacity: 0.6
    }
}
