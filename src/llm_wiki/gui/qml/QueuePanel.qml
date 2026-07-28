import QtQuick
import QtQuick.Controls

Item {
    property string title: qsTr("Queue & Raw List")

    Label {
        anchors.centerIn: parent
        text: qsTr("Ingestion queue -- wired up in Phase 15b")
        opacity: 0.6
    }
}
