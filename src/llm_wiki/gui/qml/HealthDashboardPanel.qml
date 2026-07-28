import QtQuick
import QtQuick.Controls

Item {
    property string title: qsTr("Health Dashboard")

    Label {
        anchors.centerIn: parent
        text: qsTr("Lint score charts -- wired up in Phase 15b")
        opacity: 0.6
    }
}
