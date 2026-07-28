import QtQuick
import QtQuick.Controls

Item {
    property string title: qsTr("AI Chat")

    Label {
        anchors.centerIn: parent
        text: qsTr("Vault chat -- wired up in Phase 15d")
        opacity: 0.6
    }
}
